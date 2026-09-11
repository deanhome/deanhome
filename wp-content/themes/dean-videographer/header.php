<?php
/**
 * Header template.
 */
if ( ! defined( 'ABSPATH' ) ) {
	exit;
}
?><!DOCTYPE html>
<html <?php language_attributes(); ?> class="no-js">
<head>
	<meta charset="<?php bloginfo( 'charset' ); ?>">
	<meta name="viewport" content="width=device-width, initial-scale=1">
	<script>document.documentElement.classList.replace('no-js','js');</script>
	<?php wp_head(); ?>
</head>
<body <?php body_class(); ?>>
<?php wp_body_open(); ?>

<header class="site-header" id="site-header">
	<div class="container">
		<a class="site-logo" href="<?php echo esc_url( home_url( '/' ) ); ?>">
			<?php if ( has_custom_logo() ) : ?>
				<?php the_custom_logo(); ?>
			<?php else : ?>
				<?php $brand = dean_mod( 'brand_name', 'DEAN' ); ?>
				<?php echo esc_html( $brand[0] ?? '' ) . '<span>' . esc_html( substr( $brand, 1 ) ) . '</span>'; ?>
			<?php endif; ?>
		</a>

		<nav class="main-nav" id="main-nav">
			<?php
			if ( has_nav_menu( 'primary' ) ) {
				wp_nav_menu( array(
					'theme_location' => 'primary',
					'container'      => false,
					'menu_class'     => '',
				) );
			} else {
				?>
				<ul>
					<li><a href="#about">About</a></li>
					<li><a href="#services">Services</a></li>
					<li><a href="#work">Work</a></li>
					<li><a href="#equipment">Equipment</a></li>
					<li><a href="#process">Process</a></li>
					<li><a href="#contact">Contact</a></li>
				</ul>
				<?php
			}
			?>
		</nav>

		<button class="nav-toggle" id="nav-toggle" aria-label="<?php esc_attr_e( 'Toggle menu', 'dean-videographer' ); ?>" aria-controls="main-nav" aria-expanded="false">
			<span></span><span></span><span></span>
		</button>
	</div>
</header>
