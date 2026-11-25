MACHINE ?= bonsai
PORT ?= 8080
BASE ?= http://127.0.0.1:$(PORT)
PY ?= python3

.PHONY: help install test run deploy setup models tunnel tunnel-platform trial logs status clean

help:
	@echo "Fast Image Lab — Bonsai Image 4B on kube-labs"
	@echo
	@echo "  make install   local venv + dev dependencies"
	@echo "  make test      run the test suite"
	@echo "  make run       run the gateway locally (needs a ComfyUI endpoint)"
	@echo "  make setup     provision the remote machine (torch, ComfyUI, GGUF node)"
	@echo "  make models    download the CPU profile weights on the remote machine"
	@echo "  make deploy    upload this repo and install/restart both services"
	@echo "  make tunnel    publish the UI on a Cloudflare quick tunnel"
	@echo "  make trial     submit one image and time it"
	@echo "  make logs      follow the gateway log on the remote machine"
	@echo "  make status    show engine health"
	@echo
	@echo "  MACHINE=$(MACHINE) PORT=$(PORT)"

install:
	$(PY) -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r requirements.txt pytest pytest-asyncio

test:
	.venv/bin/python -m pytest -q

run:
	.venv/bin/uvicorn gateway.main:app --host 127.0.0.1 --port $(PORT) --reload

setup:
	kube-labs exec $(MACHINE) -- mkdir -p /srv/anotherlab/scripts
	kube-labs cp scripts/remote_setup.sh $(MACHINE):/srv/anotherlab/scripts/remote_setup.sh
	kube-labs cp scripts/remote_models.sh $(MACHINE):/srv/anotherlab/scripts/remote_models.sh
	kube-labs exec $(MACHINE) -- bash /srv/anotherlab/scripts/remote_setup.sh

models:
	kube-labs exec $(MACHINE) -- bash /srv/anotherlab/scripts/remote_models.sh

deploy:
	./scripts/deploy.sh $(MACHINE)

tunnel:
	./scripts/cloudflared_tunnel.sh $(MACHINE)

tunnel-platform:
	./scripts/tunnel.sh $(MACHINE) $(PORT)

trial:
	./scripts/trial.sh $(BASE)

logs:
	kube-labs exec $(MACHINE) -- journalctl -u anotherlab -u comfyui -n 80 -f

status:
	curl -s $(BASE)/api/health | jq .

clean:
	rm -rf .venv .pytest_cache **/__pycache__
