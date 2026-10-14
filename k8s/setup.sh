#!/usr/bin/env bash
# One-command real cluster for AEGIS: kind + Online Boutique v0.10.5 + metrics-server + Chaos Mesh.
set -euo pipefail
CLUSTER=${CLUSTER:-aegis}
OB=https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/v0.10.5/release/kubernetes-manifests.yaml

if ! kind get clusters | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --wait 120s
fi
kubectl config use-context "kind-$CLUSTER"

echo ">> Online Boutique"
kubectl apply -f "$OB"
kubectl delete svc frontend-external --ignore-not-found   # no LoadBalancer on kind

echo ">> AEGIS patches: cart reads REDIS_ADDR from a ConfigMap (fault 1), redis /data gets a size limit (fault 5)"
kubectl create configmap cart-config --from-literal=REDIS_ADDR=redis-cart:6379 --dry-run=client -o yaml | kubectl apply -f -
kubectl set env deploy/cartservice --from=configmap/cart-config
kubectl patch deploy redis-cart --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/volumes/0/emptyDir","value":{"sizeLimit":"100Mi"}}]'

echo ">> metrics-server"
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml
kubectl -n kube-system patch deploy metrics-server --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'

if command -v helm >/dev/null; then
  echo ">> Chaos Mesh (faults 2 and 6)"
  helm repo add chaos-mesh https://charts.chaos-mesh.org >/dev/null 2>&1 || true
  helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh -n chaos-mesh --create-namespace --version 2.7.2 \
    --set chaosDaemon.runtime=containerd --set chaosDaemon.socketPath=/run/containerd/containerd.sock \
    --set dashboard.create=false
else
  echo "!! helm not found: faults 2 and 6 will be unavailable"
fi

echo ">> waiting for the app"
kubectl wait --for=condition=available deploy --all --timeout=600s
echo "Done. Start AEGIS against it with:  make backend-k8s"
