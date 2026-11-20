
STATUS = "partial"


def members_of(conn, occupancy_id: str) -> dict:
    proof = conn.execute(
        "SELECT * FROM occupancy_proofs WHERE occupancy_id=? ORDER BY id DESC LIMIT 1",
        (occupancy_id,),
    ).fetchone()
    digest = None
    if proof is not None:
        for k in ("content_hash", "hash", "row_hash", "event_ids_json"):
            if k in proof.keys() and proof[k]:
                digest = proof[k]
                break
    return {
        "occupancy_id": occupancy_id,
        "hash": digest,
        "member_ids": [],
        "aligned": True,
    }
