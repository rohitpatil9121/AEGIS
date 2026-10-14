PY := backend/.venv/Scripts/python
.PHONY: setup backend backend-k8s cluster-down dashboard experiments fault-% cleanup cluster
setup:            ## install backend + dashboard deps
	python -m venv backend/.venv && $(PY) -m pip install -r backend/requirements.txt
	npm --prefix frontend install
backend:          ## API + control loop on :8000 (simulated cluster)
	$(PY) -m uvicorn aegis.api:app --app-dir backend --port 8000
backend-k8s:      ## same, but driving the real cluster in the current kubectl context
	AEGIS_CLUSTER=k8s $(PY) -m uvicorn aegis.api:app --app-dir backend --port 8000
dashboard:        ## dashboard on :5173
	npm --prefix frontend run dev
experiments:      ## E1/E3/E4 x 5 seeds -> backend/results.csv
	cd backend && .venv/Scripts/python -m aegis.experiments
fault-%:          ## inject fault N into the running system, e.g. make fault-1
	curl -s -X POST localhost:8000/api/fault/$*
cleanup:
	curl -s -X POST localhost:8000/api/cleanup
cluster:          ## real cluster (M1): kind + Online Boutique + metrics-server + Chaos Mesh
	bash k8s/setup.sh
cluster-down:
	kind delete cluster --name aegis
