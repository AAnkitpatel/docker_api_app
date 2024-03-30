from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
import aiohttp
import logging
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware



# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    filename='app.log',  # Log to this file
                    filemode='a')  # Append mode

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Specify domains as needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
app.mount("/static", StaticFiles(directory="./static"), name="static")

@app.get("/")
async def main():
    return RedirectResponse(url='/static/index.html')

class DeployContainerRequest(BaseModel):
    image_name: str
    command: str = None

async def pull_image(session, image_name):
    async with session.post(f'http://192.168.1.18:2375/images/create?fromImage={image_name}',
                            headers={"Content-Type": "application/json"}) as pull_response:
        if pull_response.status in [200, 204]:
            logging.info(f"Image {image_name} pulled successfully")
            return True
        else:
            logging.error(f"Failed to pull image {image_name}, status code: {pull_response.status}")
            return False

@app.post("/deploy/")
async def deploy_container(request: DeployContainerRequest):
    image_name = request.image_name
    command = request.command
    logging.info(f"Received request to deploy image: {image_name} with command: {command}")
    async with aiohttp.ClientSession() as session:
        async with session.post(f'http://192.168.1.18:2375/containers/create',
                                headers={"Content-Type": "application/json"},
                                json={"Image": image_name, "Cmd": command.split() if command else None, "HostConfig": {"PublishAllPorts": True}}) as create_response:
            if create_response.status == 404:  # Image not found
                logging.info(f"Image {image_name} not found, attempting to pull.")
                if await pull_image(session, image_name):
                    return await deploy_container(request)  # Adjusted for Pydantic model
                else:
                    return JSONResponse(status_code=400, content={"message": f"Failed to pull image {image_name}."})
            elif create_response.status == 201:  # Container created
                create_data = await create_response.json()
                container_id = create_data.get('Id')
                async with session.post(f'http://192.168.1.18:2375/containers/{container_id}/start',
                                        headers={"Content-Type": "application/json"}) as start_response:
                    if start_response.status == 204:
                        logging.info(f"Container {image_name} started successfully with ID: {container_id}")
                        return JSONResponse(content={"message": f"Container {image_name} deployed successfully", "container_id": container_id})
                    else:
                        logging.error("Failed to start container")
                        return JSONResponse(status_code=500, content={"message": "Failed to start container"})
            else:
                logging.error("Failed to create container, status code: " + str(create_response.status))
                return JSONResponse(status_code=500, content={"message": "Failed to create container"})

class ExecuteCommandRequest(BaseModel):
    container_id: str
    command: str

@app.post("/exec/")
async def execute_command(request: ExecuteCommandRequest):
    container_id = request.container_id
    command = request.command.split()
    logging.info(f"Executing command in container: {container_id}")
    async with aiohttp.ClientSession() as session:
        # Create exec instance
        exec_create_url = f'http://192.168.1.18:2375/containers/{container_id}/exec'
        exec_create_payload = {
            "AttachStdout": True,
            "AttachStderr": True,
            "Cmd": command
        }
        async with session.post(exec_create_url, json=exec_create_payload) as exec_create_response:
            exec_create_data = await exec_create_response.json()
            exec_id = exec_create_data.get('Id')
            # Start the exec instance
            exec_start_url = f'http://192.168.1.18:2375/exec/{exec_id}/start'
            exec_start_payload = {"Detach": False, "Tty": False}
            async with session.post(exec_start_url, json=exec_start_payload) as exec_start_response:
                if exec_start_response.status == 200:
                    logging.info(f"Command executed in container: {container_id}")
                    return JSONResponse(content={"message": "Command executed successfully"})
                else:
                    logging.error("Failed to execute command")
                    return JSONResponse(status_code=500, content={"message": "Failed to execute command"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
