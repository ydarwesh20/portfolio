'use strict';

for (const slider of document.querySelectorAll('.grade-range')) {
  const comparison = slider.closest('.grade-swipe');
  const update = () => {
    comparison.style.setProperty('--split', slider.value + '%');
    slider.setAttribute('aria-valuetext', slider.value + '% LOG visible');
  };
  slider.addEventListener('input', update);
  comparison.classList.add('is-draggable');

  let pointer = null;
  function moveAt(clientX) {
    const bounds = comparison.getBoundingClientRect();
    if (!bounds.width) return;
    const fraction = Math.min(1, Math.max(0, (clientX - bounds.left) / bounds.width));
    slider.value = String(Math.round(fraction * 100));
    update();
  }
  comparison.addEventListener('pointerdown', event => {
    if (pointer !== null || !event.isPrimary || event.button !== 0) return;
    pointer = event.pointerId;
    comparison.setPointerCapture(pointer);
    if (event.pointerType !== 'touch') slider.focus({preventScroll: true});
    moveAt(event.clientX);
  });
  comparison.addEventListener('pointermove', event => {
    if (event.pointerId === pointer) moveAt(event.clientX);
  });
  comparison.addEventListener('pointerup', event => {
    if (event.pointerId === pointer) moveAt(event.clientX);
  });
  comparison.addEventListener('lostpointercapture', event => {
    if (event.pointerId === pointer) pointer = null;
  });
  update();
}
