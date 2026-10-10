# Supply an openEuler/RISC-V image from your own verified registry.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN dnf install -y python3 && dnf clean all
COPY counter.py /app/counter.py
ENTRYPOINT ["python3", "-u", "/app/counter.py"]
