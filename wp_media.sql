SELECT
    p.ID AS attachment_id,
    p.post_title AS title,
    SUBSTRING_INDEX(f.meta_value, '/', -1) AS file_name,
    f.meta_value AS file_path,
    p.post_mime_type AS mime_type,
    p.post_date AS registered_at,
    p.post_modified AS modified_at,
    p.post_author AS author_id,
    p.post_parent AS parent_post_id,
    p.post_status AS status,
    p.post_excerpt AS caption,
    p.post_content AS description,
    alt.meta_value AS alt_text,
    md.meta_value AS metadata_raw
FROM wp_posts AS p
LEFT JOIN wp_postmeta AS f
    ON f.post_id = p.ID
   AND f.meta_key = '_wp_attached_file'
LEFT JOIN wp_postmeta AS alt
    ON alt.post_id = p.ID
   AND alt.meta_key = '_wp_attachment_image_alt'
LEFT JOIN wp_postmeta AS md
    ON md.post_id = p.ID
   AND md.meta_key = '_wp_attachment_metadata'
WHERE p.post_type = 'attachment'
ORDER BY p.ID;