<?php
/**
 * Customizer settings so the site owner can edit key copy from
 * WP Admin > Appearance > Customize without touching code.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

function dean_customize_register( $wp_customize ) {

	$wp_customize->add_section( 'dean_content', array(
		'title'    => __( 'Videographer Site Content', 'dean-videographer' ),
		'priority' => 30,
	) );

	$fields = array(
		'brand_name'       => array( 'label' => 'Brand / Logo Text', 'default' => 'DEAN', 'type' => 'text' ),
		'hero_kicker'      => array( 'label' => 'Hero Kicker', 'default' => 'Videographer & Director of Photography', 'type' => 'text' ),
		'hero_heading'     => array( 'label' => 'Hero Heading', 'default' => 'Stories, framed in motion.', 'type' => 'text' ),
		'hero_subheading'  => array( 'label' => 'Hero Subheading', 'default' => 'Cinematic video production for brands, weddings and films — crafted with a sharp eye for light, pace and detail.', 'type' => 'textarea' ),
		'about_heading'    => array( 'label' => 'About Heading', 'default' => 'Behind the lens', 'type' => 'text' ),
		'about_text'       => array( 'label' => 'About Text', 'default' => 'I\'m a videographer and director of photography with over a decade behind the camera, translating ideas into footage that feels as good as it looks. From boardrooms to ceremonies, I bring a filmmaker\'s eye to every frame — composed, patient and deliberate.', 'type' => 'textarea' ),
		'stat_1_number'    => array( 'label' => 'Stat 1 Number', 'default' => '12+', 'type' => 'text' ),
		'stat_1_label'     => array( 'label' => 'Stat 1 Label', 'default' => 'Years Shooting', 'type' => 'text' ),
		'stat_2_number'    => array( 'label' => 'Stat 2 Number', 'default' => '240', 'type' => 'text' ),
		'stat_2_label'     => array( 'label' => 'Stat 2 Label', 'default' => 'Projects Delivered', 'type' => 'text' ),
		'stat_3_number'    => array( 'label' => 'Stat 3 Number', 'default' => '30+', 'type' => 'text' ),
		'stat_3_label'     => array( 'label' => 'Stat 3 Label', 'default' => 'Awards & Features', 'type' => 'text' ),
		'contact_email'    => array( 'label' => 'Contact Email', 'default' => 'hello@deanvideography.com', 'type' => 'text' ),
		'contact_phone'    => array( 'label' => 'Contact Phone', 'default' => '+1 (555) 019-2847', 'type' => 'text' ),
		'social_instagram' => array( 'label' => 'Instagram URL', 'default' => 'https://instagram.com', 'type' => 'text' ),
		'social_vimeo'     => array( 'label' => 'Vimeo URL', 'default' => 'https://vimeo.com', 'type' => 'text' ),
		'social_youtube'   => array( 'label' => 'YouTube URL', 'default' => 'https://youtube.com', 'type' => 'text' ),
		'footer_text'      => array( 'label' => 'Footer Copyright Text', 'default' => '© ' . date( 'Y' ) . ' Dean Videography. All rights reserved.', 'type' => 'text' ),
	);

	foreach ( $fields as $id => $field ) {
		$wp_customize->add_setting( $id, array(
			'default'           => $field['default'],
			'sanitize_callback' => 'textarea' === $field['type'] ? 'sanitize_textarea_field' : 'sanitize_text_field',
		) );

		$wp_customize->add_control( $id, array(
			'label'   => $field['label'],
			'section' => 'dean_content',
			'type'    => $field['type'],
		) );
	}
}
add_action( 'customize_register', 'dean_customize_register' );
