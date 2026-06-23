from fastapi import FastAPI

app = FastAPI(title="TOMS API")


@app.get("/health")
def health_check():
    return {"status": "ok"}