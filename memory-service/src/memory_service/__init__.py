def main() -> None:
    import uvicorn

    uvicorn.run("memory_service.main:app", host="0.0.0.0", port=8000)
