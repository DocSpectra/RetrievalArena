ARG BASE_IMAGE="mcr.microsoft.com/devcontainers/python@sha256:7876580dc67fd460fd962f004cbeb480027e9bbc0657096f1087db11f9eaff39"
FROM ${BASE_IMAGE}

ARG SOURCE_COMMIT
ARG SOURCE_TREE
ARG DEPENDENCY_LOCK_SHA256
ARG TARGET_OCI_PLATFORM="linux/amd64"
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
RUN python -m pip install \
      --disable-pip-version-check \
      --no-cache-dir \
      --only-binary=:all: \
      --require-hashes \
      -r /opt/retrieval-arena/requirements.lock

COPY containers/verification/verify.py /opt/retrieval-arena/verify.py
COPY containers/verification/evaluate_profile.py /opt/retrieval-arena/evaluate_profile.py
RUN test -x /usr/bin/git \
    && mkdir -p /workspace/source \
    && chown 65532:65532 /workspace/source

USER 65532:65532
WORKDIR /workspace/source
ENTRYPOINT ["python", "-I", "-B", "-X", "utf8", "/opt/retrieval-arena/verify.py"]
