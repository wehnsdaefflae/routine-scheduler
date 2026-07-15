// Scroll-follow ("stick to the newest message") — shared by the run view and conversations.
// Only an UPWARD move pauses following: content growth pushes the bottom away without any
// scroll of ours, and a symmetric "user left the bottom" check would read that as a manual
// scroll — silently pausing follow on every busy run. Scrolling back to the bottom resumes.
// `margin` is the px band above the true bottom that still counts as "at the bottom".
// Returns the teardown function (removes the listener).

export function followScroll({ margin = 80, pause, resume }) {
  let lastY = window.scrollY;
  const onScroll = () => {
    const y = window.scrollY;
    const up = y < lastY - 1;
    lastY = y;
    const atBottom = window.innerHeight + y >= document.body.scrollHeight - margin;
    if (up && !atBottom) pause();
    else if (atBottom) resume();
  };
  window.addEventListener("scroll", onScroll);
  return () => window.removeEventListener("scroll", onScroll);
}
