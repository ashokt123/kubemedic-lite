FROM python:3.12-slim
RUN pip install --no-cache-dir kubernetes==31.0.0
COPY kubemedic.py /app/kubemedic.py
USER 65534
ENTRYPOINT ["python", "-u", "/app/kubemedic.py"]
