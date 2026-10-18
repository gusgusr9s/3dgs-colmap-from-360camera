// Pure state helpers, shared by editor interactions and regression tests.
export function validBounds(value) {
  return Array.isArray(value.min) && Array.isArray(value.max) && value.min.length === 3 && value.max.length === 3 &&
    value.min.every((v,i) => Number.isFinite(v) && Number.isFinite(value.max[i]) && value.max[i]-v > 1e-6);
}
export function newCropHistory(initial) {
  const copy = value => JSON.parse(JSON.stringify(value));
  let states = [copy(initial)], index = 0;
  return {
    current: () => copy(states[index]),
    push(value) {
      if (JSON.stringify(value) === JSON.stringify(states[index])) return;
      states = states.slice(0,index+1); states.push(copy(value));
      if (states.length > 40) states.shift(); index = states.length-1;
    },
    undo() { if (index) --index; return this.current(); },
    redo() { if (index < states.length-1) ++index; return this.current(); },
    canUndo: () => index > 0, canRedo: () => index < states.length-1,
  };
}
export function previewGate() {
  let revision = 0, accepted = -1;
  return {change: () => ++revision, current: () => revision,
    accept(value) { if (value !== revision) return false; accepted = value; return true; },
    ready: () => accepted === revision};
}
