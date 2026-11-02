"""Test run config has one home."""
from __future__ import annotations
import pytest
from app.services.run_config import apply_demo_rule
from research_engine.runconfig import RunConfig
REAL = RunConfig(llm_mode='real', models={'planner': 'google:gemini-2.5-flash'})
SCRIPTED = RunConfig(llm_mode='fake', models={'planner': 'google:gemini-2.5-flash'})

def test_a_run_the_requester_asked_to_be_a_demo_is_scripted_and_stamped():
    config, stamp = apply_demo_rule(REAL, row_demo=True, host_is_scripted=False)
    assert config.llm_mode == 'fake'
    assert config.demo is True
    assert stamp is False, 'the row already says demo; there is nothing to stamp'

def test_a_run_on_a_scripted_deployment_is_recorded_as_a_demo_even_though_nobody_asked():
    """The bug this rule exists for. `start.sh` falls back to `LLM_MODE=fake` when there is
    no provider key, so this is the commonest first-run setup — and it used to record
    `demo = false` while nothing had been called."""
    config, stamp = apply_demo_rule(SCRIPTED, row_demo=False, host_is_scripted=True)
    assert config.demo is True
    assert stamp is True, 'the row says demo=false and must be corrected to match reality'

def test_a_host_wide_scripted_flag_counts_even_when_the_config_says_real():
    """The desktop's `--fake` is a process flag, not a `RunConfig` field. Both ways in have
    to reach the same branch, or one host records honestly and the other does not."""
    config, stamp = apply_demo_rule(REAL, row_demo=False, host_is_scripted=True)
    assert config.llm_mode == 'fake'
    assert config.demo is True
    assert stamp is True

def test_an_ordinary_run_is_left_alone():
    config, stamp = apply_demo_rule(REAL, row_demo=False, host_is_scripted=False)
    assert config.llm_mode == 'real'
    assert config.demo is False
    assert stamp is False

def test_the_decision_is_stable_across_a_resume():
    """`demo` selects the seeded content while `llm_mode` keeps the run offline, so a flag
    that flipped False->True between a run and its resume would change what the run
    researches halfway through. Feeding the result back in must be a no-op."""
    first, _ = apply_demo_rule(SCRIPTED, row_demo=False, host_is_scripted=True)
    second, stamp = apply_demo_rule(first, row_demo=True, host_is_scripted=True)
    assert (second.llm_mode, second.demo) == (first.llm_mode, first.demo)
    assert stamp is False

def test_the_rule_never_downgrades_a_recorded_demo():
    """A row that says demo must stay scripted even if the host is not — otherwise a
    resumed demo would start calling a real provider mid-run."""
    config, _ = apply_demo_rule(REAL, row_demo=True, host_is_scripted=False)
    assert config.llm_mode == 'fake'
