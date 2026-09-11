<?php
/**
 * Dean Videographer 3D theme functions.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'DEAN_THEME_VERSION', '1.0.0' );

/**
 * Theme setup.
 */
function dean_theme_setup() {
	add_theme_support( 'title-tag' );
	add_theme_support( 'post-thumbnails' );
	add_theme_support( 'custom-logo', array(
		'height'      => 80,
		'width'       => 240,
		'flex-height' => true,
		'flex-width'  => true,
	) );
	add_theme_support( 'html5', array( 'search-form', 'gallery', 'caption', 'style', 'script' ) );
	add_theme_support( 'automatic-feed-links' );

	register_nav_menus( array(
		'primary' => __( 'Primary Menu', 'dean-videographer' ),
	) );
}
add_action( 'after_setup_theme', 'dean_theme_setup' );

/**
 * Enqueue styles and scripts.
 */
function dean_enqueue_assets() {
	wp_enqueue_style(
		'dean-google-fonts',
		'https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;0,600;1,500&family=Jost:wght@300;400;500;600&display=swap',
		array(),
		null
	);

	wp_enqueue_style( 'dean-style', get_stylesheet_uri(), array(), DEAN_THEME_VERSION );

	wp_enqueue_script(
		'dean-scroll',
		get_template_directory_uri() . '/assets/js/scroll.js',
		array(),
		DEAN_THEME_VERSION,
		true
	);
}
add_action( 'wp_enqueue_scripts', 'dean_enqueue_assets' );

/**
 * Small helper to read a theme_mod with a fallback default.
 */
function dean_mod( $key, $default = '' ) {
	return get_theme_mod( $key, $default );
}

require get_template_directory() . '/inc/customizer.php';
