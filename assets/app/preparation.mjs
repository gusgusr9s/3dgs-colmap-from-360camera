const floor = document.querySelector('#preparation-floor');
floor?.addEventListener('change', () => {
  for (const image of document.querySelectorAll('[data-floor-preview]')) {
    image.src = image.dataset.floorPreview + '&floor=' + encodeURIComponent(floor.value);
  }
});
