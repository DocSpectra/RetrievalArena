ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_COMMIT
ARG SOURCE_TREE
ARG DEPENDENCY_LOCK_SHA256
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      io.retrieval-arena.source.tree="${SOURCE_TREE}" \
      io.retrieval-arena.embedded-api.lock="${DEPENDENCY_LOCK_SHA256}" \
      io.retrieval-arena.embedded-api.contract="retrieval_arena.embedded_token_admission.v1"
USER 0:0
COPY retrieval_arena /opt/embedded-api/retrieval_arena
WORKDIR /opt/embedded-api
ENV PYTHONDONTWRITEBYTECODE=1 HOME=/tmp TMPDIR=/tmp \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
USER 65532:65532
ENTRYPOINT ["python", "-B", "-m", "retrieval_arena.embedded_token_admission"]
