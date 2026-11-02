FROM python:3.14-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /workspace
COPY requirements.txt ./
RUN pip install -r requirements.txt
RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser . ./ui
USER appuser
EXPOSE 8501
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health',timeout=3)"
CMD ["streamlit", "run", "ui/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--browser.gatherUsageStats=false", "--client.toolbarMode=minimal"]

FROM runtime AS test
USER root
RUN pip install pytest==9.1.1
USER appuser
ENTRYPOINT ["python", "-m", "pytest"]
CMD ["ui/tests", "-q", "-p", "no:cacheprovider"]
