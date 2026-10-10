#!/usr/bin/env python3
"""Emit platform API/RBAC manifests."""
import json
KINDS=[("ResourceClaim","resourceclaims","rclaim"),("ResourcePolicy","resourcepolicies","rpolicy"),
       ("NodeProfile","nodeprofiles","nprofile"),("HostNetworkWorkload","hostnetworkworkloads","hnet"),
       ("ScenarioPolicy","scenariopolicies","scene")]
def crd(kind,plural,short):
    return {"apiVersion":"apiextensions.k8s.io/v1","kind":"CustomResourceDefinition",
      "metadata":{"name":plural+".platform.riscv.io"},"spec":{"group":"platform.riscv.io","scope":"Namespaced",
       "names":{"kind":kind,"plural":plural,"singular":kind.lower(),"shortNames":[short]},
       "versions":[{"name":"v1alpha1","served":True,"storage":True,"subresources":{"status":{}},
        "schema":{"openAPIV3Schema":{"type":"object","properties":{
          "spec":{"type":"object","x-kubernetes-preserve-unknown-fields":True},
          "status":{"type":"object","x-kubernetes-preserve-unknown-fields":True}}}},
        "additionalPrinterColumns":[{"name":"Phase","type":"string","jsonPath":".status.phase"},
           {"name":"Generation","type":"integer","jsonPath":".status.observedGeneration"}]}]}}
def manifests():
    name="rv-platform-controller"
    objects=[{"apiVersion":"v1","kind":"Namespace","metadata":{"name":"rv-platform"}}]
    objects += [crd(*entry) for entry in KINDS]
    rules=[
       {"apiGroups":["platform.riscv.io"],"resources":["*"],"verbs":["get","list","watch","update","patch"]},
       {"apiGroups":[""],"resources":["nodes"],"verbs":["get","list","patch"]},
       {"apiGroups":[""],"resources":["pods"],"verbs":["get","list","create","delete"]},
       {"apiGroups":["apps"],"resources":["deployments"],"verbs":["get","list","create","patch"]},
       {"apiGroups":["autoscaling"],"resources":["horizontalpodautoscalers"],"verbs":["get","list"]},
       {"apiGroups":["migration.riscv.io"],"resources":["nodemigrations"],"verbs":["get","list","create"]}]
    objects += [
       {"apiVersion":"v1","kind":"ServiceAccount","metadata":{"name":name,"namespace":"rv-platform"}},
       {"apiVersion":"rbac.authorization.k8s.io/v1","kind":"ClusterRole","metadata":{"name":name},"rules":rules},
       {"apiVersion":"rbac.authorization.k8s.io/v1","kind":"ClusterRoleBinding","metadata":{"name":name},
        "roleRef":{"apiGroup":"rbac.authorization.k8s.io","kind":"ClusterRole","name":name},
        "subjects":[{"kind":"ServiceAccount","name":name,"namespace":"rv-platform"}]},
       {"apiVersion":"v1","kind":"Secret","metadata":{"name":name+"-token","namespace":"rv-platform",
        "annotations":{"kubernetes.io/service-account.name":name}},"type":"kubernetes.io/service-account-token"}]
    return {"apiVersion":"v1","kind":"List","items":objects}
if __name__=="__main__": print(json.dumps(manifests(),indent=2))
