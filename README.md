# calendar_backend

Python service-layer backend for task planning, scheduling, calendar assignment, and free-time allocation.

The implementation source of truth is the updated V1 engineering design document. Finalized implementation plans live in `docs/plans/`.

## V3 HTTP API

Run the FastAPI server (local single-user dev):

```bash
uv run calendar-backend-api
```

- Health: `GET http://127.0.0.1:8000/health`
- OpenAPI: `http://127.0.0.1:8000/docs`

See [`docs/v3_engineering_design.md`](docs/v3_engineering_design.md) for the API contract and [`docs/frontend/v1_setup.md`](docs/frontend/v1_setup.md) for frontend repo setup.