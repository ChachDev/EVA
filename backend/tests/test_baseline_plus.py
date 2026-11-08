from copy import deepcopy

import pytest

from app.audit import checked, fingerprint
from app.ml_client import MLInvalidResponse


def make_bundle(contracts,request):
    result=contracts.example("audit-bundle")
    audit=result["audit"]
    audit["versions"].update(profile="baseline-plus",model=result["response"]["model"],
                             transcript=fingerprint(request["conversation"]),checklist=fingerprint(request["checklist"]))
    audit["verification"]={"baseline":deepcopy(result["response"]),"state":"not_needed","requested":[],"checks":{}}
    return result


def test_primary_result_is_bound_and_unchecked_rows_cannot_be_rewritten(contracts,request_data):
    bundle=make_bundle(contracts,request_data)
    checked(contracts,request_data,bundle)
    changed=deepcopy(bundle)
    changed["response"]["criteria"][0]["reason"]="Replaced silently."
    with pytest.raises(MLInvalidResponse):checked(contracts,request_data,changed)
    bad=deepcopy(bundle);bad["audit"]["verification"]["baseline"]["checklist_version"]+=1
    with pytest.raises(MLInvalidResponse):checked(contracts,request_data,bad)


def test_amendment_needs_a_verified_source(contracts,request_data):
    bundle=make_bundle(contracts,request_data)
    first=bundle["response"]["criteria"][0]
    first["value"]=not first["value"]
    bundle["audit"]["verification"]["checks"][first["criterion_id"]]={"outcome":"amended","reason":"New fact."}
    with pytest.raises(MLInvalidResponse):checked(contracts,request_data,bundle)
