<?php
/**
 * Footer template.
 */
if ( ! defined( 'ABSPATH' ) ) {
	exit;
}
?>
	<footer class="site-footer">
		<div class="container">
			<p class="footer-fine"><?php echo esc_html( dean_mod( 'footer_text', '© ' . date( 'Y' ) . ' Dean Videography. All rights reserved.' ) ); ?></p>
			<ul class="footer-social">
				<li><a href="<?php echo esc_url( dean_mod( 'social_instagram', '#' ) ); ?>" target="_blank" rel="noopener">Instagram</a></li>
				<li><a href="<?php echo esc_url( dean_mod( 'social_vimeo', '#' ) ); ?>" target="_blank" rel="noopener">Vimeo</a></li>
				<li><a href="<?php echo esc_url( dean_mod( 'social_youtube', '#' ) ); ?>" target="_blank" rel="noopener">YouTube</a></li>
			</ul>
		</div>
	</footer>

<?php wp_footer(); ?>
</body>
</html>
