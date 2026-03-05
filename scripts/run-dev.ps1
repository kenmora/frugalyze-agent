$args = @(
  "app.main:app",
  "--reload",
  "--host",
  "127.0.0.1",
  "--port",
  "8000",
  "--reload-dir",
  "app",
  "--reload-dir",
  "static"
)

uvicorn @args
