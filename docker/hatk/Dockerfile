FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/hlagent \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

COPY docker/hatk/install.sh docker/hatk/requirements.txt /opt/hlagent/
COPY hlagent/__init__.py hlagent/paths.py /opt/hlagent/hlagent/
COPY hlagent/analysis /opt/hlagent/hlagent/analysis
COPY vendor/hatk /opt/hlagent/vendor/hatk
RUN sh /opt/hlagent/install.sh

WORKDIR /work
USER 10001:10001
ENTRYPOINT ["python", "-u", "-m", "hlagent.analysis.hatk_worker"]
CMD ["--check"]
