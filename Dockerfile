FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS packages
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.lock /tmp/requirements.lock
COPY wheelhouse /wheelhouse
RUN python -m pip install --no-index --find-links=/wheelhouse --require-hashes --no-deps --target=/opt/deps -r /tmp/requirements.lock

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
WORKDIR /app
COPY --from=packages /opt/deps /opt/deps
COPY service.py sequence_planner.py ./
ENV PYTHONPATH=/opt/deps PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1000:1000
ENTRYPOINT ["python","/app/service.py"]
