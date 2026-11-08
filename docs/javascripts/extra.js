window.addEventListener("DOMContentLoaded", function () {
  var links = document.querySelectorAll('.md-typeset a[href^="http"]');
  links.forEach(function (a) {
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  });
});
