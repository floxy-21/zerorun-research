FROM python@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a
COPY wheelhouse/ /opt/zerorun-wheelhouse/
COPY requirements.lock /opt/zerorun-requirements.lock
RUN python -m pip install --disable-pip-version-check --no-cache-dir --no-index --find-links=/opt/zerorun-wheelhouse --require-hashes -r /opt/zerorun-requirements.lock
COPY q1q2_capture.py /opt/q1q2/q1q2_capture.py
ENV PYTHONPATH=/workspace/src:/workspace:/opt/q1q2
LABEL org.opencontainers.image.title=ZeroRun-compatible-handoff-dependencies
COPY --chmod=755 ftfy /usr/local/bin/ftfy
COPY debs/ /opt/q1q2-debs/
RUN DEBIAN_FRONTEND=noninteractive dpkg -i /opt/q1q2-debs/*.deb && localedef -i en_US -f UTF-8 en_US.UTF-8 && localedef -i de_DE -f UTF-8 de_DE.UTF-8 && localedef -i cs_CZ -f UTF-8 cs_CZ.UTF-8
ENV HOME=/tmp HYPOTHESIS_STORAGE_DIRECTORY=/tmp/hypothesis
