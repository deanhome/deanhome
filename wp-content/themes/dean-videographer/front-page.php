<?php
/**
 * Front page template — one-page cinematic videographer portfolio
 * with 3D scroll-reveal sections.
 */
if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

get_header();

$theme_uri = get_template_directory_uri();
?>

<main id="main-content">

	<!-- ============ HERO ============ -->
	<section class="hero">
		<div class="hero-bg-wrap">
			<div class="hero-bg" id="hero-bg"></div>
			<div class="hero-grain"></div>
		</div>
		<div class="container">
			<p class="hero-kicker"><?php echo esc_html( dean_mod( 'hero_kicker', 'Videographer & Director of Photography' ) ); ?></p>
			<h1><?php echo wp_kses_post( dean_mod( 'hero_heading', 'Stories, framed in motion.' ) ); ?></h1>
			<p class="hero-sub"><?php echo esc_html( dean_mod( 'hero_subheading', 'Cinematic video production for brands, weddings and films — crafted with a sharp eye for light, pace and detail.' ) ); ?></p>
			<div class="hero-cta">
				<a href="#work" class="btn btn-solid">View Showreel</a>
				<a href="#contact" class="btn">Book a Project</a>
			</div>
		</div>
		<div class="scroll-cue"><span>Scroll</span><span class="line"></span></div>
	</section>

	<!-- ============ ABOUT ============ -->
	<section class="section scene" id="about">
		<div class="container about">
			<div class="reveal" data-effect="tilt-left">
				<div class="about-portrait">
					<img src="<?php echo esc_url( $theme_uri ); ?>/assets/images/portrait-dean.png" alt="<?php esc_attr_e( 'Portrait of the videographer', 'dean-videographer' ); ?>" loading="lazy">
					<div class="about-portrait-frame"></div>
				</div>
			</div>
			<div class="reveal" data-effect="tilt-right">
				<p class="eyebrow">About</p>
				<h2><?php echo esc_html( dean_mod( 'about_heading', 'Behind the lens' ) ); ?></h2>
				<p><?php echo esc_html( dean_mod( 'about_text', "I'm a videographer and director of photography with over a decade behind the camera, translating ideas into footage that feels as good as it looks. From boardrooms to ceremonies, I bring a filmmaker's eye to every frame — composed, patient and deliberate." ) ); ?></p>
				<div class="about-stats">
					<div>
						<strong><?php echo esc_html( dean_mod( 'stat_1_number', '12+' ) ); ?></strong>
						<span><?php echo esc_html( dean_mod( 'stat_1_label', 'Years Shooting' ) ); ?></span>
					</div>
					<div>
						<strong><?php echo esc_html( dean_mod( 'stat_2_number', '240' ) ); ?></strong>
						<span><?php echo esc_html( dean_mod( 'stat_2_label', 'Projects Delivered' ) ); ?></span>
					</div>
					<div>
						<strong><?php echo esc_html( dean_mod( 'stat_3_number', '30+' ) ); ?></strong>
						<span><?php echo esc_html( dean_mod( 'stat_3_label', 'Awards & Features' ) ); ?></span>
					</div>
				</div>
			</div>
		</div>
	</section>

	<!-- ============ SERVICES ============ -->
	<section class="section section-alt scene" id="services">
		<div class="container">
			<div class="section-head reveal" data-effect="rise">
				<p class="eyebrow">Services</p>
				<h2>What I bring to set</h2>
			</div>
			<div class="services-grid reveal-group">
				<?php
				$services = array(
					array( 'Commercial', 'Brand films and product spots shot to convert, edited to keep attention.' ),
					array( 'Weddings', 'Documentary-style coverage of your day, cut into a film you\'ll actually rewatch.' ),
					array( 'Music Videos', 'Concept-driven visuals built around the track\'s energy and story.' ),
					array( 'Corporate', 'Training, culture and event video that looks as sharp as the brand behind it.' ),
					array( 'Aerial / Drone', 'FAA-certified aerial coverage for scale, scope and cinematic establishing shots.' ),
					array( 'Post-Production', 'Editing, color grading and sound design that ties every shot together.' ),
				);
				foreach ( $services as $i => $service ) :
					?>
					<div class="service-card reveal" data-effect="rise">
						<span class="num"><?php echo esc_html( str_pad( $i + 1, 2, '0', STR_PAD_LEFT ) ); ?></span>
						<h3><?php echo esc_html( $service[0] ); ?></h3>
						<p><?php echo esc_html( $service[1] ); ?></p>
					</div>
				<?php endforeach; ?>
			</div>
		</div>
	</section>

	<!-- ============ SHOWREEL ============ -->
	<section class="section scene" id="work">
		<div class="container">
			<div class="section-head reveal" data-effect="rise">
				<p class="eyebrow">Featured Work</p>
				<h2>The showreel</h2>
			</div>
			<div class="showreel reveal" data-effect="zoom">
				<a class="showreel-frame" href="#" aria-label="<?php esc_attr_e( 'Play showreel', 'dean-videographer' ); ?>">
					<span class="showreel-play"></span>
				</a>
			</div>
		</div>
	</section>

	<!-- ============ EQUIPMENT (parallax band) ============ -->
	<section class="equipment" id="equipment">
		<div class="equipment-bg" id="equipment-bg"></div>
		<div class="container">
			<div class="section-head reveal" data-effect="rise">
				<p class="eyebrow">On Location</p>
				<h2>The kit behind the shot</h2>
			</div>
			<ul class="equipment-list reveal" data-effect="rise">
				<li><span>Cinema Camera</span><span>6K RAW body, dual native ISO</span></li>
				<li><span>Prime Lenses</span><span>Full cinema prime set, T1.5</span></li>
				<li><span>Stabilization</span><span>Gimbal + fluid head tripod</span></li>
				<li><span>Aerial</span><span>FAA Part 107 licensed drone</span></li>
				<li><span>Audio</span><span>Wireless lav + shotgun mic kit</span></li>
				<li><span>Lighting</span><span>LED panel + tungsten Fresnel kit</span></li>
			</ul>
		</div>
	</section>

	<!-- ============ PROCESS ============ -->
	<section class="section scene" id="process">
		<div class="container">
			<div class="section-head reveal" data-effect="rise">
				<p class="eyebrow">Process</p>
				<h2>From concept to delivery</h2>
			</div>
			<div class="process-grid reveal-group">
				<?php
				$steps = array(
					array( 'Concept', 'We define the story, the shot list and the deliverables before anything is booked.' ),
					array( 'Shoot', 'On-set direction, lighting and camera work focused on getting it right in-camera.' ),
					array( 'Edit', 'Structure, pacing and color grading in a private review cut for your notes.' ),
					array( 'Deliver', 'Final files exported for every platform you need — web, social and broadcast.' ),
				);
				foreach ( $steps as $step ) :
					?>
					<div class="process-step reveal" data-effect="flip-up">
						<h3><?php echo esc_html( $step[0] ); ?></h3>
						<p><?php echo esc_html( $step[1] ); ?></p>
					</div>
				<?php endforeach; ?>
			</div>
		</div>
	</section>

	<!-- ============ TESTIMONIALS ============ -->
	<section class="section section-alt scene">
		<div class="container">
			<div class="section-head reveal" data-effect="rise">
				<p class="eyebrow">Kind Words</p>
				<h2>What clients say</h2>
			</div>
			<div class="testimonial-grid reveal-group">
				<?php
				$quotes = array(
					array( 'Dean turned our launch footage into something we\'re still using a year later. Effortless to work with.', 'M. Alvarez', 'Brand Director' ),
					array( 'Our wedding film made half the room cry. He captured moments we didn\'t even know happened.', 'S. & J. Whitfield', 'Clients' ),
					array( 'Fast, professional, and the aerial shots elevated the entire project.', 'R. Okafor', 'Producer' ),
				);
				foreach ( $quotes as $q ) :
					?>
					<div class="testimonial-card reveal" data-effect="rise">
						<p>&ldquo;<?php echo esc_html( $q[0] ); ?>&rdquo;</p>
						<div class="testimonial-name">
							<span class="avatar-dot"><?php echo esc_html( mb_substr( $q[1], 0, 1 ) ); ?></span>
							<div>
								<strong><?php echo esc_html( $q[1] ); ?></strong><br>
								<span><?php echo esc_html( $q[2] ); ?></span>
							</div>
						</div>
					</div>
				<?php endforeach; ?>
			</div>
		</div>
	</section>

	<!-- ============ CTA / CONTACT ============ -->
	<section class="cta scene" id="contact">
		<div class="container">
			<p class="eyebrow reveal" data-effect="rise" style="justify-content:center;">Let's Work Together</p>
			<h2 class="reveal" data-effect="rise">Have a story worth filming?</h2>
			<a href="mailto:<?php echo esc_attr( dean_mod( 'contact_email', 'hello@deanvideography.com' ) ); ?>" class="btn btn-solid reveal" data-effect="zoom">Start a Project</a>
			<div class="contact-details reveal" data-effect="rise">
				<a href="mailto:<?php echo esc_attr( dean_mod( 'contact_email', 'hello@deanvideography.com' ) ); ?>"><?php echo esc_html( dean_mod( 'contact_email', 'hello@deanvideography.com' ) ); ?></a>
				<a href="tel:<?php echo esc_attr( preg_replace( '/[^0-9+]/', '', dean_mod( 'contact_phone', '+1 (555) 019-2847' ) ) ); ?>"><?php echo esc_html( dean_mod( 'contact_phone', '+1 (555) 019-2847' ) ); ?></a>
			</div>
		</div>
	</section>

</main>

<?php get_footer(); ?>
