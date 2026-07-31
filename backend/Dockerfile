FROM apache/airflow:3.3.0

# Pipeline deps only. The API deps (fastapi/uvicorn/pydantic) would conflict
# with the versions Airflow pins internally.
COPY requirements-pipeline.txt /tmp/requirements-pipeline.txt
RUN pip install --no-cache-dir -r /tmp/requirements-pipeline.txt
