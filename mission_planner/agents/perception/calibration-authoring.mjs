import assert from 'node:assert/strict';

export async function withCalibrationSession(command, action) {
  const initial = await command({ type: 'snapshot' });
  assert.equal(initial.authoring.position, null, 'Calibration requires an empty task-owned session');
  const restore = () => command({ type: 'reset' });
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
