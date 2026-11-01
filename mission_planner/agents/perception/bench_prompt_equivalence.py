"""Recorded decision equivalence through the same SAM and crop-verification pipeline."""

import argparse
import asyncio
import json
from pathlib import Path

from mission_planner.agents.models import Glimmer, Sam
from mission_planner.agents.perception.bench_commander import provenance
from mission_planner.agents.perception.compare_prompts import compare
from mission_planner.agents.perception.configuration import (
    digest,
    encode,
    load_configuration,
    read_capture,
)
from mission_planner.agents.perception.ingestion import load_replay
from mission_planner.agents.perception.pipeline import Processor


async def run(manifest, prompts, config, sam, glimmer, output):
    assert len(prompts) == 3 and "person" not in prompts and len(set(prompts)) == 3
    processors = {
        "dedicated": Processor(output / "dedicated", ["person"], config, sam, glimmer),
        "combined": Processor(
            output / "combined",
            prompts,
            {
                **config,
                "person_batching": "verified_decisions",
                "person_batching_prompts": ["person", *prompts],
            },
            sam,
            glimmer,
        ),
    }
    slots = asyncio.Semaphore(8)

    async def variant(row, name):
        processor = processors[name]
        async with slots:
            record, payload = await asyncio.to_thread(read_capture, row["record"])
            prepared = await asyncio.to_thread(
                processor.detect,
                record,
                payload,
                row["frame_id"],
                "recorded",
                row["replay_lane"],
                lambda stage: None,
            )
        result = await asyncio.to_thread(processor.verify, prepared, lambda stage: None)
        return (
            row["frame_id"],
            name,
            {
                "detections": result["detections"],
                "sam_responses": [
                    json.loads(
                        processor.directory.joinpath(row["frame_id"] + "-sam-0.json").read_text()
                    )
                ],
                "timings": result["timings"],
                "input_sha256": digest(prepared["resized"]),
            },
        )

    pending = [
        asyncio.create_task(variant(row, name))
        for row in manifest["records"]
        for name in processors
    ]
    rows = {}
    output.joinpath("frames").mkdir()
    try:
        for task in asyncio.as_completed(pending):
            identity, name, result = await task
            if identity not in rows:
                rows[identity] = {
                    "frame_id": identity,
                    "input_sha256": result["input_sha256"],
                    "variants": {},
                }
            assert rows[identity]["input_sha256"] == result["input_sha256"]
            rows[identity]["variants"][name] = result
            output.joinpath("frames", identity + ".json").write_text(encode(rows[identity]) + "\n")
    finally:
        outcomes = await asyncio.gather(*pending, return_exceptions=True)
        failures = [
            {"index": i, "error": f"{type(value).__name__}: {value}"}
            for i, value in enumerate(outcomes)
            if isinstance(value, BaseException)
        ]
        output.joinpath("failures.json").write_text(encode(failures) + "\n")
    assert not failures
    result = compare(list(rows.values()), config["sam_threshold"], config["glimmer_threshold"])
    result.update(
        manifest_id=manifest["id"],
        prompts=["person", *prompts],
        source="Recorded frames",
        experiment="Combined configuration is experimental until every decision-equivalence gate passes; no belief updates are made",
        sam_responses="Exact validated candidate values saved by Sam.detect; HTTP timing and server metadata are in each variant's requests sidecar",
    )
    output.joinpath("comparison.json").write_text(encode(result) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--prompt", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest, (config, _) = load_replay(args.manifest), load_configuration()
    assert len(manifest["records"]) == 490, (
        "Decision equivalence requires the full 490-frame corpus"
    )
    assert config["person_batching"] == "dedicated"
    args.output.mkdir(parents=True, exist_ok=False)
    provenance(args.output, "Captured before clients and comparison requests")
    args.output.joinpath("configuration.json").write_text(encode(config) + "\n")
    sam, glimmer = Sam(), None
    try:
        glimmer = Glimmer()
        result = asyncio.run(run(manifest, args.prompt, config, sam, glimmer, args.output))
    finally:
        sam.http.close()
        if glimmer is not None:
            glimmer.http.close()
    print(
        encode(
            {
                key: result[key]
                for key in (
                    "frames",
                    "all_equivalent",
                    "unmatched_candidates",
                    "changed_candidate_decisions",
                    "minimum_observed_iou",
                    "maximum_observed_score_difference",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
