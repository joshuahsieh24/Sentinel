from fastapi import FastAPI
app = FastAPI(title="Sentinel")
@app.get("/")
async def index():
    return {"message": "Sentinel Initialized"}
