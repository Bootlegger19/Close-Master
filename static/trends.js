// Trends tab: draws the chart, and resets the date range when "Group by" changes.
(function () {
  const canvas = document.getElementById("trend-chart");
  if (canvas) {
    if (typeof Chart === "undefined") {
      canvas.parentElement.textContent =
        "The chart library couldn't load (check your internet connection). " +
        "The table below has the same numbers.";
    } else {
      const chart = JSON.parse(canvas.dataset.chart);
      const palette = ["#0072B2", "#E69F00", "#009E73", "#D55E00",
                       "#CC79A7", "#56B4E9", "#F0E442", "#666666"];
      const isLine = chart.style === "line";

      new Chart(canvas, {
        type: isLine ? "line" : "bar",
        data: {
          labels: chart.labels,
          datasets: chart.datasets.map(function (dataset, index) {
            const color = palette[index % palette.length];
            return {
              label: dataset.label,
              data: dataset.data,          // null = no closing in that period
              backgroundColor: color,
              borderColor: color,
              borderWidth: isLine ? 2 : 0,
              pointRadius: isLine ? 3 : 0,
              tension: 0.2,
              spanGaps: true,
            };
          }),
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          scales: {
            y: { beginAtZero: true, title: { display: true, text: chart.y_label } },
          },
          plugins: { legend: { position: "bottom" } },
        },
      });
    }
  }

  const group = document.getElementById("trend-group");
  const from = document.getElementById("trend-from");
  const to = document.getElementById("trend-to");
  if (group && from && to) {
    const spans = JSON.parse(group.dataset.spans);
    group.addEventListener("change", function () {
      const end = new Date(to.value || new Date().toISOString().slice(0, 10));
      end.setUTCDate(end.getUTCDate() - spans[group.value]);
      from.value = end.toISOString().slice(0, 10);
    });
  }
})();