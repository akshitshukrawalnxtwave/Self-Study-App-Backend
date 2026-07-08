/* quiz.js — shared quiz widget for lessons.
   Markup contract:
     <div class="quiz" data-answer="1">
       <div class="q">Question text…</div>
       <button class="opt">Option A</button>   // index 0
       <button class="opt">Option B</button>   // index 1  (correct here)
       <button class="opt">Option C</button>   // index 2
       <div class="fb" data-good="Nice…" data-bad="Not quite…"></div>
     </div>
   The correct option index is data-answer. Feedback strings live on .fb.
   Immediate, automatic feedback; keeps trying until correct. */

document.addEventListener("click", function (e) {
  var btn = e.target.closest(".quiz .opt");
  if (!btn) return;

  var quiz = btn.closest(".quiz");
  var opts = Array.prototype.slice.call(quiz.querySelectorAll(".opt"));
  var idx = opts.indexOf(btn);
  var answer = parseInt(quiz.getAttribute("data-answer"), 10);
  var fb = quiz.querySelector(".fb");

  if (idx === answer) {
    opts.forEach(function (o) { o.classList.remove("wrong"); });
    btn.classList.add("correct");
    if (fb) { fb.textContent = fb.getAttribute("data-good") || "Correct."; fb.className = "fb good"; }
  } else {
    btn.classList.add("wrong");
    if (fb) { fb.textContent = fb.getAttribute("data-bad") || "Try again."; fb.className = "fb bad"; }
  }
});
