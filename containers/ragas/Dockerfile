FROM python@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4
ARG SOURCE_COMMIT=development
ARG SOURCE_TREE=development
ARG DEPENDENCY_LOCK_SHA256=development
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      io.retrieval-arena.source.tree="${SOURCE_TREE}" \
      io.retrieval-arena.ragas.contract="retrieval_arena.ragas_image.v1" \
      io.retrieval-arena.ragas.dependency-lock-sha256="${DEPENDENCY_LOCK_SHA256}"
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 \
    PYTHONIOENCODING=utf-8 RAGAS_DO_NOT_TRACK=true HF_HUB_OFFLINE=1 \
    LANGCHAIN_TRACING_V2=false RA_RAGAS_NETWORK=none
COPY containers/ragas/requirements.lock /opt/ra/containers/ragas/requirements.lock
RUN python -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: \
    --require-hashes -r /opt/ra/containers/ragas/requirements.lock && python -m pip check
COPY retrieval_arena /opt/ra/retrieval_arena
COPY unit_tests/ragas_fixture.py unit_tests/test_ragas_scoring.py unit_tests/test_ragas_api.py unit_tests/test_model_providers.py /opt/ra/unit_tests/
COPY containers/ragas/ragas_operator.py /opt/ra/containers/ragas/ragas_operator.py
WORKDIR /opt/ra
USER 65532:65532
ENTRYPOINT ["python", "-B", "/opt/ra/containers/ragas/ragas_operator.py"]
