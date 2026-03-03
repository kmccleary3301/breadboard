.PHONY: help setup setup-fast setup-engine setup-fast-engine setup-refresh-python setup-tui setup-smoke setup-sdk-live setup-all doctor doctor-full doctor-tui quickstart cli-capabilities onboarding-contract e4-target-manifest disk-report disk-prune repair-cli build-tui typecheck-tui smoke sdk-hello sdk-hello-live devx-smoke devx-smoke-engine devx-smoke-live devx-full-pass devx-full-pass-engine devx-timing

help:
	@echo "BreadBoard dev shortcuts"
	@echo "  make setup           # full bootstrap (python + sdk/ts + tui)"
	@echo "  make setup-fast      # full bootstrap without doctor (fast repeat runs)"
	@echo "  make setup-engine    # python/engine-only bootstrap"
	@echo "  make setup-fast-engine # engine bootstrap without doctor (fast repeat runs)"
	@echo "  make setup-refresh-python # force Python dependency reinstall in existing .venv"
	@echo "  make setup-tui       # node/tui-only bootstrap"
	@echo "  make setup-smoke     # bootstrap + unit smoke"
	@echo "  make setup-sdk-live  # bootstrap + live sdk hello verification"
	@echo "  make setup-all       # bootstrap + unit smoke + live sdk hello verification"
	@echo "  make doctor          # strict first-time doctor (engine profile)"
	@echo "  make doctor-full     # strict first-time doctor (full profile)"
	@echo "  make doctor-tui      # strict first-time doctor (tui profile)"
	@echo "  make quickstart      # show recommended next commands (CLI-independent)"
	@echo "  make cli-capabilities # detect active breadboard CLI feature support"
	@echo "  make onboarding-contract # check onboarding script/docs contract drift"
	@echo "  make e4-target-manifest # validate E4 harness target freeze manifest coverage"
	@echo "  make disk-report    # dry-run ~/.breadboard cleanup plan + JSON report"
	@echo "  make disk-prune     # apply ~/.breadboard cleanup with safe defaults"
	@echo "  make repair-cli      # rebuild and validate local breadboard CLI wrapper"
	@echo "  make smoke           # unit-only switcher smoke"
	@echo "  make sdk-hello-live  # live python+ts sdk hello check"
	@echo "  make devx-smoke      # verify onboarding/devx command paths"
	@echo "  make devx-smoke-engine # engine-only onboarding/devx command path smoke"
	@echo "  make devx-smoke-live # devx smoke + live sdk hello verification"
	@echo "  make devx-full-pass  # full sequential onboarding confidence pass + JSON report"
	@echo "  make devx-full-pass-engine # engine-only full pass (safe while TUI is in flux)"
	@echo "  make devx-timing     # summarize latest devx full-pass timing report"

setup:
	bash scripts/dev/bootstrap_first_time.sh

setup-fast:
	bash scripts/dev/bootstrap_first_time.sh --no-doctor

setup-engine:
	bash scripts/dev/bootstrap_first_time.sh --profile engine

setup-fast-engine:
	bash scripts/dev/bootstrap_first_time.sh --profile engine --no-doctor

setup-refresh-python:
	bash scripts/dev/bootstrap_first_time.sh --profile engine --refresh-python-deps --no-doctor

setup-tui:
	bash scripts/dev/bootstrap_first_time.sh --profile tui

setup-smoke:
	bash scripts/dev/bootstrap_first_time.sh --smoke

setup-sdk-live:
	bash scripts/dev/bootstrap_first_time.sh --sdk-hello-live

setup-all:
	bash scripts/dev/bootstrap_first_time.sh --all-checks

doctor:
	python scripts/dev/first_time_doctor.py --profile engine --strict

doctor-full:
	python scripts/dev/first_time_doctor.py --strict

doctor-tui:
	python scripts/dev/first_time_doctor.py --profile tui --strict

quickstart:
	python scripts/dev/quickstart_first_time.py --include-advanced

cli-capabilities:
	python scripts/dev/cli_capabilities.py --json

onboarding-contract:
	python scripts/dev/check_onboarding_contract_drift.py --strict

e4-target-manifest:
	python scripts/check_e4_target_freeze_manifest.py --json

disk-report:
	python scripts/prune_breadboard_home.py --json-out artifacts/maintenance/prune_breadboard_home.latest.json

disk-prune:
	python scripts/prune_breadboard_home.py --apply --json-out artifacts/maintenance/prune_breadboard_home.latest.json

repair-cli:
	bash scripts/dev/repair_cli_wrapper.sh

build-tui:
	npm -C tui_skeleton ci
	npm -C tui_skeleton run build

typecheck-tui:
	npm -C tui_skeleton run typecheck

smoke:
	./scripts/smoke_switcher_fancy_use_cases.sh --no-live

sdk-hello:
	python scripts/dev/python_sdk_hello.py
	node scripts/dev/ts_sdk_hello.mjs

sdk-hello-live:
	bash scripts/dev/sdk_hello_live_smoke.sh

devx-smoke:
	bash scripts/dev/devx_smoke.sh

devx-smoke-engine:
	bash scripts/dev/devx_smoke.sh --profile engine

devx-smoke-live:
	bash scripts/dev/devx_smoke.sh --live

devx-full-pass:
	bash scripts/dev/full_pass_validate.sh

devx-full-pass-engine:
	BREADBOARD_FULL_PASS_PROFILE=engine bash scripts/dev/full_pass_validate.sh

devx-timing:
	python scripts/dev/report_devx_timing.py
