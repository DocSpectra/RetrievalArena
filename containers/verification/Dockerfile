ARG BASE_IMAGE="mcr.microsoft.com/devcontainers/python@sha256:7876580dc67fd460fd962f004cbeb480027e9bbc0657096f1087db11f9eaff39"
FROM ${BASE_IMAGE}
USER root

ARG SOURCE_COMMIT
ARG SOURCE_TREE
ARG DEPENDENCY_LOCK_SHA256
ARG TARGET_OCI_PLATFORM="linux/amd64"
ARG PROVISIONED="false"
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      io.retrieval-arena.source.tree="${SOURCE_TREE}" \
      io.retrieval-arena.verification.contract="retrieval_arena.portable_verification.v1" \
      io.retrieval-arena.verification.dependency-lock-sha256="${DEPENDENCY_LOCK_SHA256}" \
      io.retrieval-arena.verification.platform="${TARGET_OCI_PLATFORM}"

ENV HOME=/tmp \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONIOENCODING=utf-8 \
    TMPDIR=/workspace/source/.tmp_unit_tests \
    TZ=UTC

COPY containers/verification/requirements.lock /opt/retrieval-arena/requirements.lock
RUN if [ "$PROVISIONED" = "true" ]; then \
      python -c "import importlib.metadata as m; from pathlib import Path; rows=[r.split(' --hash=')[0].split('==') for r in Path('/opt/retrieval-arena/requirements.lock').read_text().splitlines() if r and not r.startswith('#')]; assert all(m.version(n)==v for n,v in rows); import numpy, scipy, ir_measures, pytrec_eval"; \
    else python -m pip install \
      --disable-pip-version-check \
      --no-cache-dir \
      --only-binary=:all: \
      --require-hashes \
      -r /opt/retrieval-arena/requirements.lock; fi

COPY containers/verification/verify.py /opt/retrieval-arena/verify.py
COPY containers/verification/evaluate_profile.py /opt/retrieval-arena/evaluate_profile.py
COPY containers/verification/review_packet.py /opt/retrieval-arena/review_packet.py
COPY containers/verification/candidate_retrieval.py /opt/retrieval-arena/candidate_retrieval.py
COPY containers/verification/docstyle_measurement.py /opt/retrieval-arena/docstyle_measurement.py
COPY containers/verification/graph_spectra.py /opt/retrieval-arena/graph_spectra.py
COPY containers/verification/magnetic_attribution.py /opt/retrieval-arena/magnetic_attribution.py
RUN test -x /usr/bin/git \
    && mkdir -p /workspace/source \
    && chown 65532:65532 /workspace/source

USER 65532:65532
WORKDIR /workspace/source
ENTRYPOINT ["python", "-I", "-B", "-X", "utf8", "/opt/retrieval-arena/verify.py"]
