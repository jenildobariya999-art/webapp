const ctx = document.getElementById('activityChart').getContext('2d');
new Chart(ctx, {
    type: 'line',
    data: {
        labels: ['Thu', 'Fri', 'Sat', 'Sun', 'Mon', 'Tue', 'Wed'],
        datasets: [{
            label: 'Credit',
            data: [110, 20, 30, 10, 45, 10, 10],
            borderColor: '#7b61ff',
            tension: 0.4, // This makes the line curved
            fill: true,
            backgroundColor: 'rgba(123, 97, 255, 0.1)'
        }]
    },
    options: {
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } }
    }
});
