# Preserve the existing scientific environment and add checked example inputs.
ARG ANALYSIS_IMAGE=hlagent-hatk:layout-v1
FROM ${ANALYSIS_IMAGE}
COPY hlagent/analysis /opt/hlagent/hlagent/analysis
COPY examples/hatk /opt/hlagent/examples/hatk
