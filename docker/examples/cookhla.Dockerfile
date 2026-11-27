# Preserve the handed-over CookHLA environment and add checked example inputs.
ARG ANALYSIS_IMAGE=jaejoon/cookhla:v1
FROM ${ANALYSIS_IMAGE}
COPY examples/cookhla /opt/hlagent/examples/cookhla
