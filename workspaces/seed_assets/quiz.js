(function () {
  document.querySelectorAll("[data-quiz]").forEach(function (el) {
    var answer = el.dataset.answer;
    var btn = el.querySelector("button");
    var feedback = el.querySelector(".feedback");
    if (!btn || !feedback) return;
    btn.addEventListener("click", function () {
      var input = el.querySelector("input");
      if (!input) return;
      var correct = input.value.trim().toLowerCase() === answer.toLowerCase();
      feedback.textContent = correct ? "Correct!" : "Try again.";
      feedback.style.color = correct ? "green" : "crimson";
    });
  });
})();
