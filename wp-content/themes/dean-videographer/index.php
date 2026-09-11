<?php
/**
 * Fallback template (blog index / generic archive).
 * The primary experience lives in front-page.php.
 */
if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

get_header();
?>
<main id="main-content" class="section">
	<div class="container">
		<?php if ( have_posts() ) : ?>
			<?php while ( have_posts() ) : the_post(); ?>
				<article <?php post_class( 'section-alt reveal is-in' ); ?> style="padding:2.4rem; border-radius:6px; margin-bottom:1.6rem;">
					<h2><a href="<?php the_permalink(); ?>"><?php the_title(); ?></a></h2>
					<div><?php the_excerpt(); ?></div>
				</article>
			<?php endwhile; ?>
			<?php the_posts_pagination(); ?>
		<?php else : ?>
			<p><?php esc_html_e( 'Nothing found.', 'dean-videographer' ); ?></p>
		<?php endif; ?>
	</div>
</main>
<?php get_footer(); ?>
