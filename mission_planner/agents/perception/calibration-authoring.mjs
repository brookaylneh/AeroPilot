import assert from 'node:assert/strict';

export async function withScenarioBackup(command, action) {
  const initial = await command({ type: 'snapshot' });
  let restore;
  if (initial.authoring.position === null) {
    assert.equal(initial.lastSavedId, '', 'Unplaced scenario must have no saved identity');
    restore = async () => {
      await command({ type: 'reset' });
      await command({ type: 'region', regionId: initial.authoring.regionId });
      await command({ type: 'appearance', appearance: initial.authoring.appearance });
      await command({ type: 'situation', situationId: initial.authoring.situationId });
      await command({ type: 'rotate', yaw: initial.authoring.rotation.yaw });
      await command({ type: 'briefing', briefing: initial.briefing });
      const restored = await command({ type: 'creator-stage', stage: initial.creatorStage });
      assert.deepEqual(restored.authoring, initial.authoring, 'Unplaced authoring state changed');
      assert.deepEqual(restored.briefing, initial.briefing, 'Unplaced briefing changed');
      assert.equal(restored.lastSavedId, initial.lastSavedId);
      assert.equal(restored.creatorStage, initial.creatorStage);
    };
  } else {
    assert(initial.authoring.position && typeof initial.authoring.position === 'object');
    const backup = await command({ type: 'save', name: 'Before calibration', summary: 'Private evaluation' });
    restore = () => command({ type: 'load', id: backup.lastSavedId });
  }
  let failure;
  try {
    return await action();
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    try {
      await restore();
    } catch (error) {
      if (failure) {
        throw new AggregateError(
          [failure, error],
          `Calibration failed: ${failure.message}; scenario restoration failed: ${error.message}`,
          { cause: failure },
        );
      }
      throw new Error(`Scenario restoration failed: ${error.message}`, { cause: error });
    }
  }
}
