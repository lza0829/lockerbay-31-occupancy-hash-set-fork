from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import seed
from app.routers import api

app = FastAPI(title="格湾 LockerBay", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    seed.init_db()


app.include_router(api)


@app.get("/api/health")
def health():
    return {"正常": True, "项目": "格湾", "slug": "lockerbay"}
