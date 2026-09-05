# W5.4 remote-world mask decision

Status: COMPLETE

The pre-registered world-equivalence mask contains exactly these JSON pointers:

```json
{
  "schema_version": "bb.world_field_mask.v1",
  "paths": [
    "/occurred_at",
    "/timestamp"
  ]
}
```

No provider identity, request identity, status, output, side-effect, ordering, or
failure field is masked. A local-process, OCI, Ray-actor, or Slurm result that
differs outside these two clock fields is product-red.
