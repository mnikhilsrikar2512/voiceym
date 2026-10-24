/** Engines whose adapters actually forward sampling steps to their model. */
export function samplingStepRange(engineId?: string | null): [number, number] | null {
  if (['omnivoice', 'omnivoice-subprocess', 'voxcpm2'].includes(engineId ?? ''))
    return [8, 64];
  return null;
}

export function effectiveSamplingSteps(steps: number, engineId?: string | null): number {
  const range = samplingStepRange(engineId);
  return range ? Math.min(range[1], Math.max(range[0], steps)) : steps;
}
