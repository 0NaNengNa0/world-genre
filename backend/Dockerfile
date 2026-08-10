FROM apache/airflow:3.3.0

# Pipeline deps only, and deliberately self-contained (no `-r` references to
# other files) - anything it referenced would also have to be COPYd here, and
# forgetting that breaks the build in a way that isn't obvious from this file.
# The API deps (fastapi/uvicorn/pydantic) are excluded on purpose; see the
# note at the top of requirements-pipeline.txt.
COPY requirements-pipeline.txt /tmp/requirements-pipeline.txt
RUN pip install --no-cache-dir -r /tmp/requirements-pipeline.txt
