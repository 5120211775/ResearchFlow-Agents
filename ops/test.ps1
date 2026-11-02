$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
docker compose -f compose.yml --profile test up -d --build test-api test-worker test-ui
if ($LASTEXITCODE) { exit $LASTEXITCODE }
docker compose -f compose.yml --profile test --profile tools build backend-tests ui-tests
if ($LASTEXITCODE) { exit $LASTEXITCODE }
$backendChecks = @(
    'tests/task/test_ui_api.py'
    'tests/task/test_runs_api.py'
    'tests/task/test_endpoint_identity.py'
    'tests/task/test_model_routing.py'
    'tests/task/test_prompt_override_preferences.py'
    'tests/task/test_run_model_routing.py'
    'tests/workflow/test_pipeline.py'
    'tests/workflow/test_plan_gate.py'
    'tests/workflow/test_run_execution_integration.py'
    'tests/workflow/test_critic_failclosed.py'
    'tests/workflow/test_corpus_upload_contract.py'
    'tests/workflow/test_metrics_recording.py'
    'tests/workflow/test_prompt_defaults_route.py'
    'tests/workflow/test_rework_keeps_evidence_and_citations_honest.py'
    'tests/workflow/test_run_correlation.py'
    'tests/workflow/test_stream_replay_rules.py'
    'tests/workflow/test_memory_index_port.py'
    'tests/workflow/test_capabilities_are_observable.py'
    'tests/dataflow/test_project_memory.py'
    'tests/security/test_artifact_authorization.py'
    'tests/security/test_provider_health.py'
    'tests/security/test_ssrf_guard.py'
)
docker compose -f compose.yml --profile test run --rm --no-deps backend-tests @backendChecks -q -p no:cacheprovider --tb=short
if ($LASTEXITCODE) { exit $LASTEXITCODE }
docker compose -f compose.yml --profile test run --rm --no-deps ui-tests
exit $LASTEXITCODE

