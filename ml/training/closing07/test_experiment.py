import numpy as np

from training.closing07.data import cases
from training.closing07.experiment import fit


def test_all_endings_of_a_source_stay_together_and_are_balanced():
    from collections import defaultdict, Counter
    rows=list(cases())
    by_family=defaultdict(set)
    by_source=defaultdict(Counter)
    for row in rows:
        by_family[row["family"]].add(row["split"])
        by_source[row["source"]][row["label"]]+=1
    assert all(len(splits)==1 for splits in by_family.values())
    assert all(counts=={"A":2,"B":2,"C":2} for counts in by_source.values())
    assert len({r["source"] for r in rows if r["split"]=="train"})==6


def test_lora_factorization_matches_the_trained_three_token_logits():
    rng=np.random.default_rng(732)
    w=rng.normal(size=(3,12))
    h=rng.normal(size=(21,12))
    original=h@w.T
    logits=original-np.logaddexp.reduce(original,axis=1,keepdims=True)
    target=np.argmax(original[:,[1,0,2]],axis=1)
    b=fit(logits,target)
    a=w[:2]-w[2]
    fitted=logits+(logits[:,:2]-logits[:,2:])@b.T
    native=h@(w+b@a).T
    np.testing.assert_allclose(fitted-fitted[:,2:],native-native[:,2:],atol=1e-9)
    loss=lambda z:float(np.mean(np.logaddexp.reduce(z,axis=1)-z[np.arange(len(z)),target]))
    assert loss(fitted)<loss(logits)
