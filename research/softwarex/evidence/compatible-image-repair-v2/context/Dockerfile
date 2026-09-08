FROM python@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a
COPY wheelhouse/ /opt/zerorun-wheelhouse/
COPY requirements.lock /opt/zerorun-requirements.lock
RUN python -m pip install --disable-pip-version-check --no-cache-dir --no-index --find-links=/opt/zerorun-wheelhouse --require-hashes -r /opt/zerorun-requirements.lock
ENV PYTHONPATH=/workspace/src:/workspace
LABEL org.opencontainers.image.title=ZeroRun-compatible-handoff-dependencies
