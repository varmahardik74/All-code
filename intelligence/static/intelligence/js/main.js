document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("comparison-form");
    if (!form) {
        return;
    }

    form.addEventListener("submit", (event) => {
        const countryA = form.querySelector("#id_country_a");
        const countryB = form.querySelector("#id_country_b");
        if (countryA && countryB && countryA.value && countryA.value === countryB.value) {
            event.preventDefault();
            window.alert("Select two different countries.");
        }
    });
});
