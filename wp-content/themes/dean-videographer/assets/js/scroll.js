/**
 * Dean Videographer 3D — scroll interactions.
 * - IntersectionObserver reveal-on-scroll with 3D transforms
 * - Lightweight parallax on hero + equipment background bands
 * - Sticky header state + mobile nav toggle
 * - Subtle pointer-tilt on service cards
 * All effects are skipped gracefully for prefers-reduced-motion.
 */
(function () {
	'use strict';

	var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

	/* ---------- Scroll reveal ---------- */
	var revealEls = document.querySelectorAll('.reveal');
	if ('IntersectionObserver' in window && revealEls.length) {
		var io = new IntersectionObserver(
			function (entries) {
				entries.forEach(function (entry) {
					if (entry.isIntersecting) {
						entry.target.classList.add('is-in');
						io.unobserve(entry.target);
					}
				});
			},
			{ threshold: 0.18, rootMargin: '0px 0px -8% 0px' }
		);
		revealEls.forEach(function (el) {
			io.observe(el);
		});
	} else {
		revealEls.forEach(function (el) {
			el.classList.add('is-in');
		});
	}

	/* ---------- Sticky header ---------- */
	var header = document.getElementById('site-header');
	function onScrollHeader() {
		if (!header) return;
		if (window.scrollY > 40) {
			header.classList.add('is-scrolled');
		} else {
			header.classList.remove('is-scrolled');
		}
	}
	onScrollHeader();

	/* ---------- Mobile nav toggle ---------- */
	var toggle = document.getElementById('nav-toggle');
	var nav = document.getElementById('main-nav');
	if (toggle && nav) {
		toggle.addEventListener('click', function () {
			var isOpen = nav.classList.toggle('is-open');
			toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
		});
		nav.querySelectorAll('a').forEach(function (link) {
			link.addEventListener('click', function () {
				nav.classList.remove('is-open');
				toggle.setAttribute('aria-expanded', 'false');
			});
		});
	}

	/* ---------- Parallax bands (hero + equipment) ---------- */
	var heroBg = document.getElementById('hero-bg');
	var equipmentBg = document.getElementById('equipment-bg');
	var ticking = false;

	function updateParallax() {
		var y = window.scrollY || window.pageYOffset;

		if (heroBg) {
			var heroSpeed = 0.35;
			var heroScale = 1.08 + Math.min(y, 800) * 0.00025;
			heroBg.style.transform = 'translate3d(0,' + y * heroSpeed + 'px,0) scale(' + heroScale + ')';
		}

		if (equipmentBg) {
			var rect = equipmentBg.parentElement.getBoundingClientRect();
			var vh = window.innerHeight;
			if (rect.top < vh && rect.bottom > 0) {
				var progress = (vh - rect.top) / (vh + rect.height);
				var offset = (progress - 0.5) * 120;
				equipmentBg.style.transform = 'translate3d(0,' + offset + 'px,0)';
			}
		}

		ticking = false;
	}

	function onScroll() {
		onScrollHeader();
		if (!reduceMotion) {
			if (!ticking) {
				window.requestAnimationFrame(updateParallax);
				ticking = true;
			}
		}
	}

	window.addEventListener('scroll', onScroll, { passive: true });
	if (!reduceMotion) {
		updateParallax();
	}

	/* ---------- Pointer tilt on service / testimonial cards ---------- */
	if (!reduceMotion && window.matchMedia('(hover: hover)').matches) {
		var tiltCards = document.querySelectorAll('.service-card');
		tiltCards.forEach(function (card) {
			card.addEventListener('mousemove', function (e) {
				var rect = card.getBoundingClientRect();
				var x = (e.clientX - rect.left) / rect.width - 0.5;
				var y = (e.clientY - rect.top) / rect.height - 0.5;
				card.style.transform =
					'perspective(800px) rotateX(' + (-y * 10).toFixed(2) + 'deg) rotateY(' + (x * 10).toFixed(2) + 'deg) translateZ(6px)';
			});
			card.addEventListener('mouseleave', function () {
				card.style.transform = 'none';
			});
		});
	}

	/* ---------- Smooth-scroll for in-page anchor nav ---------- */
	document.querySelectorAll('a[href^="#"]').forEach(function (link) {
		link.addEventListener('click', function (e) {
			var id = link.getAttribute('href');
			if (id.length > 1) {
				var target = document.querySelector(id);
				if (target) {
					e.preventDefault();
					target.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
				}
			}
		});
	});
})();
