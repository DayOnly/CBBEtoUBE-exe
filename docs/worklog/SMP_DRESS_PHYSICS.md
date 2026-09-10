# Physics of the mage dresses: three clean, one real defect

2026-09-04, while the pack was being judged in game. Checked with the
CONVERTER's own XML readers (`_hdt_collider_shape_names`,
`_hdt_softbody_shape_names`) so the population is what the converter itself
acts on, not a name list.

## Clean on all three dresses

| check | result |
|---|---|
| shapes the XML registers, still present in the NIF | **0 missing** on all three |
| chain bones the XML drives, present as NODES | **0 missing** on all three |
| softbodies preserved | 2 -> 2 on each |
| colliders | 2 -> 3 (our `SkirtCol` added) |
| XML bone counts vs author | identical (96 / 91 / 77) |
| generated proxy encloses its chain | 0 |

The Lewd variants (`DressALewd`, `BLewd`, `CLewd`) carry **no HDT reference --
and neither do the author's**. Not a loss.

`VirtualGround` carries `NPC Root [Root]` undeclared on all three. **The
author's files are identical**, so it is inherited, not ours. Do not "fix" it.

## THE DEFECT: DressC's skirt collider is built from the POTION NET

    DressA  SkirtCol  428 verts, bbox z 40.9..77.6, 70 bones (MCDressA_A ...)
            median distance to `1_dress`          0.000u   <- built from the dress
    DressC  SkirtCol  457 verts, bbox x 10.5..16.0 z 73.8..79.9, 1 bone (MCPotion 1)
            median distance to `4_belt_potion_net` 0.000u   <- built from the POTION NET
            median distance to `3_dress`           4.085u

So on DressC the generated skirt collider is a collider for a potion bottle's
net, registered in the XML as the skirt collider, while **the actual skirt
(`3_dress`, z 34.8..111.3, 45 chain bones) has no collision proxy at all.**

## Mechanism NOT established -- do not assume it is the selector

`_add_skirt_collider_proxy` chooses "the largest rendered shape that is
actually simulated", implemented as the maximum COUNT of verts with chain mass
>= `_SKIRT_PROXY_CHAIN_MIN` (0.5). Reproduced on the SOURCE nifs:

    DressA   4_belt_potion_net 1519  >  1_dress 1146
    DressC   4_belt_potion_net 1519  >  3_dress  212

The potion net ranks FIRST on BOTH. If the selector alone decided it, DressA
would be wrong too -- and DressA is correct. So something downstream (the fire
gate that skips pieces whose authored proxies already track the cloth, the
donor choice, or the decl filter) differs between them and is the real
discriminator. That has not been traced.

What IS certain is the output: DressC ships a mislabelled proxy and an
unprotected skirt. A fix must explain DressA too, or it will be a single-piece
patch.

## Note on the criterion

Counting chain-driven VERTICES favours a small, densely simulated accessory
over a large garment: the potion net is 1673 verts of which 1519 are chain, the
DressC skirt is 3832 of which 212. A skirt collider wants the shape with the
skirt-like EXTENT (z-extent 76.7 vs 9.8), not the densest chain. Worth
considering if this is reopened -- but only alongside an explanation of DressA.
