"""Three static diagnostic plots from the deterministic calibration report."""

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from mission_control.environment import ROOT, load_env


def main():
    load_env()
    result = json.loads((ROOT / "measurements/calibration.json").read_text())["results"]
    assert result is not None, "No calibration measurements to plot"
    output = ROOT / "measurements/plots"
    output.mkdir(exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.5), sharey=True, layout="constrained")
    for ax, title, names in zip(
        axes,
        ["Preset", "Shirt colour", "Situation"],
        [
            ["adult-kelvin", "child-01", "child-02", "child-03", "child-04", "child-05"],
            ["adult-kelvin", "adult-kelvin-forest", "child-01", "child-01-forest"],
            ["adult-kelvin", "adult-kelvin-injured", "child-01", "child-01-injured"],
        ],
    ):
        for name in names:
            if name in result["conditions"]:
                rows = result["conditions"][name]["offsets"]
                ax.plot(
                    [r["offsetM"] for r in rows], [r["frame"]["p"] for r in rows], ".-", label=name
                )
        ax.set(title=title, xlabel="Lateral offset (m)", ylim=(-0.05, 1.05))
        ax.legend(fontsize=6)
    axes[0].set_ylabel("Frame detection fraction")
    fig.savefig(output / "detection.png", dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(8, 3), layout="constrained")
    curve = result["samCurve"]
    for ax, values, title in zip(
        axes,
        [[r["detection"]["p"] for r in curve], [r["falseAlarmsPerFrame"] for r in curve]],
        ["Detection fraction", "False alarms / empty frame"],
    ):
        ax.step([r["threshold"] for r in curve], values, where="post")
        ax.set(xlabel="SAM threshold (strict >)", ylabel=title)
    axes[1].axhline(0.01, color="red", linestyle="--")
    fig.savefig(output / "sam-threshold.png", dpi=160)
    plt.close(fig)
    rows = [r for r in result["glimmer"]["reliability"] if r[2]]
    fig, ax = plt.subplots(figsize=(4, 3), layout="constrained")
    ax.plot([0, 1], [0, 1], "--", color="gray")
    ax.scatter([r[3] for r in rows], [r[4] for r in rows], s=[10 + r[2] for r in rows])
    ax.set(xlabel="Mean P(yes)", ylabel="Observed positive fraction", xlim=(0, 1), ylim=(0, 1))
    fig.savefig(output / "glimmer-reliability.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()
